"""Single-host transactional manifests and private, content-addressed material blobs."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256, scrypt
from pathlib import Path
import hmac
import json
import os
import re
import secrets
import sqlite3
from urllib.parse import parse_qsl, urlsplit
import time

from fastapi import HTTPException

ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
EMAIL = re.compile(r"[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{1,63}\Z")
USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,63}\Z")
KINDS = frozenset(("subtitle", "transcription", "correction", "translation", "cover", "image", "ai_text", "ai_result", "analysis", "analysis-followup", "frame", "conversation", "conversation-message"))
TEXT_MIMES = frozenset(("text/plain", "text/markdown", "application/json", "application/x-subrip", "text/vtt"))
IMAGE_MIMES = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
RETENTION = 30 * 86400
SESSION_TTL = 30 * 86400


def fail(status: int, code: str, **details):
    raise HTTPException(status, {"code": code, **details})


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        fail(422, "invalid_id")
    return value


def exact_fields(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        fail(422, "invalid_fields")


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        fail(422, "invalid_json")


def account_name(value):
    """A login handle: either an e-mail address or a plain user name like Celyn."""
    if not isinstance(value, str) or not 1 <= len(value) <= 254 or any(ord(char) < 33 or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        fail(422, "invalid_login")
    if EMAIL.fullmatch(value) or USERNAME.fullmatch(value):
        return value
    fail(422, "invalid_login")


def password_hash(password, salt):
    try:
        encoded = password.encode("utf-8")
    except UnicodeError:
        fail(422, "invalid_password")
    return scrypt(encoded, salt=salt, n=32768, r=8, p=3, maxmem=64 * 1024 * 1024).hex()


def portable_text(value, maximum=500, multiline=False):
    if not isinstance(value, str) or len(value) > maximum or any((ord(char) < 32 and not (multiline and char in "\r\n\t")) or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        fail(422, "invalid_metadata")
    if re.search(r"(?:[A-Za-z]:[\\/]|file:|\\\\|(?:^|\s)/[^\s])", value, re.I):
        fail(422, "local_path_metadata")
    if re.search(r"(?:api[_-]?key|access_token|password|cookie)\s*[:=]|-----BEGIN [A-Z ]*PRIVATE KEY-----", value, re.I):
        fail(422, "secret_metadata")


def validate_metadata(metadata):
    exact_fields(metadata, ("title", "source_key", "learned", "platform", "video_id", "part", "source_url", "created_at"))
    for key, value in metadata.items():
        if key == "learned":
            if type(value) is not bool:
                fail(422, "invalid_metadata")
        elif key == "part":
            if type(value) is not int or not 1 <= value <= 100000:
                fail(422, "invalid_metadata")
        elif key == "created_at" and type(value) in (int, float):
            if not 0 <= value <= 100000000000:
                fail(422, "invalid_metadata")
        elif key == "source_url":
            try:
                url = urlsplit(value)
                valid = url.scheme in ("http", "https") and bool(url.hostname) and not url.username and not url.password and not url.fragment
                pairs = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True)
                if pairs:
                    host = (url.hostname or "").lower()
                    valid = valid and len(pairs) == 1
                    if host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
                        valid = valid and pairs[0][0] == "v" and bool(ID.fullmatch(pairs[0][1]))
                    elif host in ("bilibili.com", "www.bilibili.com", "m.bilibili.com"):
                        valid = valid and pairs[0][0] == "p" and pairs[0][1].isdigit() and 1 <= int(pairs[0][1]) <= 100000
                    else:
                        valid = False
                url.port
            except (TypeError, ValueError):
                valid = False
            if not valid:
                fail(422, "invalid_source_url")
            if not isinstance(value, str) or len(value) > 2000 or any((ord(char) < 32) or 0xD800 <= ord(char) <= 0xDFFF for char in value):
                fail(422, "invalid_metadata")
            if re.search(r"(?:api[_-]?key|access_token|password|cookie)\s*[:=]|-----BEGIN [A-Z ]*PRIVATE KEY-----", value, re.I):
                fail(422, "secret_metadata")
        else:
            portable_text(value, 500 if key == "title" else 300)
    if len(canonical(metadata).encode()) > 4096:
        fail(422, "metadata_too_large")


def validate_details(details):
    allowed = ("filename", "lang", "language", "source_language", "source_sha256", "video_part", "format", "report_type", "generated_at", "created_at", "record", "conversation", "sequence", "question", "source", "title", "model", "template", "frames", "links", "source_material_id", "origin", "label", "template_id", "template_name", "context_after", "origin_record", "position", "images")
    exact_fields(details, allowed)
    if len(canonical(details).encode()) > 65536:
        fail(422, "details_too_large")
    for key, value in details.items():
        if key in ("video_part", "sequence"):
            if type(value) is not int or not 0 <= value <= 1000000:
                fail(422, "invalid_details")
        elif key == "position" or (key in ("generated_at", "created_at") and type(value) in (int, float)):
            if type(value) not in (int, float) or not 0 <= value <= 100000000000:
                fail(422, "invalid_details")
        elif key == "template":
            if isinstance(value, str):
                portable_text(value, 500)
            else:
                exact_fields(value, ("id", "name"))
                for text in value.values():
                    portable_text(text, 300)
        elif key in ("frames", "links", "images"):
            if not isinstance(value, list) or len(value) > 200:
                fail(422, "invalid_details")
            for reference in value:
                exact_fields(reference, ("material_id", "version_id", "time") if key == "frames" else ("role", "material_id", "version_id"), ("material_id", "version_id"))
                identifier(reference["material_id"])
                identifier(reference["version_id"])
                if "role" in reference:
                    portable_text(reference["role"], 100)
                if "time" in reference and (type(reference["time"]) not in (int, float) or not 0 <= reference["time"] <= 8640000):
                    fail(422, "invalid_details")
        else:
            portable_text(value, 4000 if key in ("question", "context_after") else 500, multiline=key in ("question", "context_after"))
            if key == "filename" and ("/" in value or "\\" in value or value in ("", ".", "..")):
                fail(422, "invalid_filename")
            if key == "source_material_id":
                identifier(value)
            if key == "source_sha256" and not HASH.fullmatch(value):
                fail(422, "invalid_sha256")


def validate_blob(path: Path, mime: str, size: int):
    if mime in TEXT_MIMES:
        if size > 4 * 1024 * 1024:
            fail(413, "text_too_large")
        try:
            text = path.read_bytes().decode("utf-8")
            if "\x00" in text:
                fail(422, "invalid_text")
            if mime == "application/json":
                json.loads(text, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        except (UnicodeError, ValueError, RecursionError):
            fail(422, "invalid_text")
        return
    if mime not in IMAGE_MIMES:
        fail(415, "unsupported_mime")
    from PIL import Image
    import warnings
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format != IMAGE_MIMES[mime] or image.width * image.height > 40_000_000 or getattr(image, "is_animated", False):
                    fail(422, "invalid_image")
                image.verify()
            # JPEG verify() does not decode pixels; load separately to reject truncated data.
            with Image.open(path) as image:
                image.load()
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        fail(422, "invalid_image")


class Store:
    def __init__(self, root: Path, *, max_blob_bytes=32 * 1024 * 1024, account_quota_bytes=2 * 1024**3, clock=time.time):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.blob_root = self.root / "blobs"
        self.temp_root = self.root / "staging"
        self.blob_root.mkdir(exist_ok=True, mode=0o700)
        self.temp_root.mkdir(exist_ok=True, mode=0o700)
        self.database = self.root / "cloud.sqlite3"
        self.max_blob_bytes = max_blob_bytes
        self.account_quota_bytes = account_quota_bytes
        self.clock = clock
        self.dummy_salt = secrets.token_bytes(16)
        self.dummy_hash = password_hash(secrets.token_hex(32), self.dummy_salt)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, login TEXT NOT NULL UNIQUE, salt TEXT NOT NULL, password TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    owner TEXT NOT NULL REFERENCES users(id), id TEXT NOT NULL, document TEXT NOT NULL,
                    PRIMARY KEY(owner,id)
                );
                CREATE TABLE IF NOT EXISTS blobs (
                    owner TEXT NOT NULL, project TEXT NOT NULL, digest TEXT NOT NULL, size INTEGER NOT NULL,
                    mime TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(owner,project,digest),
                    FOREIGN KEY(owner,project) REFERENCES projects(owner,id)
                );
                CREATE TABLE IF NOT EXISTS operations (
                    owner TEXT NOT NULL REFERENCES users(id), id TEXT NOT NULL, digest TEXT NOT NULL,
                    result TEXT NOT NULL, PRIMARY KEY(owner,id)
                );
                CREATE TABLE IF NOT EXISTS login_limits (
                    key TEXT PRIMARY KEY, started REAL NOT NULL, attempts INTEGER NOT NULL
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
            if "login" not in columns and "email" in columns:
                # Databases created before user names were allowed keep their accounts.
                db.execute("ALTER TABLE users RENAME COLUMN email TO login")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_login_lower ON users(lower(login))")
        if os.name == "posix":
            self.root.chmod(0o700)
            self.database.chmod(0o600)

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA synchronous=FULL")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def create_user(self, login, password):
        login = account_name(login)
        if not isinstance(password, str) or not 12 <= len(password) <= 1024:
            fail(422, "password_length_12_to_1024")
        salt = secrets.token_bytes(16)
        user_id = secrets.token_hex(16)
        hashed = password_hash(password, salt)
        with self.connect(True) as db:
            try:
                db.execute("INSERT INTO users VALUES (?,?,?,?)", (user_id, login, salt.hex(), hashed))
            except sqlite3.IntegrityError:
                fail(409, "login_exists")
        return {"id": user_id, "login": login}

    def rename_user(self, login, new_login):
        login = account_name(login)
        new_login = account_name(new_login)
        with self.connect(True) as db:
            row = db.execute("SELECT id FROM users WHERE lower(login)=lower(?)", (login,)).fetchone()
            if not row:
                fail(404, "login_not_found")
            try:
                db.execute("UPDATE users SET login=? WHERE id=?", (new_login, row["id"]))
            except sqlite3.IntegrityError:
                fail(409, "login_exists")
        return {"id": row["id"], "login": new_login}

    def login(self, login, password, client):
        login = account_name(login)
        if not isinstance(password, str) or not 1 <= len(password) <= 1024:
            fail(401, "invalid_credentials")
        now = self.clock()
        limited = False
        with self.connect(True) as db:
            db.execute("DELETE FROM login_limits WHERE started <= ?", (now - 60,))
            for value in ("login:" + login.casefold(), "ip:" + client):
                key = sha256(value.encode()).hexdigest()
                row = db.execute("SELECT attempts FROM login_limits WHERE key=?", (key,)).fetchone()
                if row and row[0] >= 10:
                    limited = True
                db.execute("INSERT INTO login_limits VALUES (?,?,1) ON CONFLICT(key) DO UPDATE SET attempts=attempts+1", (key, now))
            user = db.execute("SELECT * FROM users WHERE lower(login)=lower(?)", (login,)).fetchone()
        if limited:
            fail(429, "login_rate_limited")
        salt = bytes.fromhex(user["salt"]) if user else self.dummy_salt
        expected = user["password"] if user else self.dummy_hash
        valid = hmac.compare_digest(password_hash(password, salt), expected)
        if not valid or not user:
            fail(401, "invalid_credentials")
        token = secrets.token_urlsafe(32)
        expires = now + SESSION_TTL
        with self.connect(True) as db:
            db.execute("DELETE FROM sessions WHERE expires <= ?", (now,))
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (sha256(token.encode()).hexdigest(), user["id"], expires))
        return {"token": token, "expires_at": expires, "user": {"id": user["id"], "login": user["login"]}}

    def session(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 128:
            fail(401, "authentication_required")
        digest = sha256(token.encode()).hexdigest()
        with self.connect() as db:
            row = db.execute("SELECT users.id,users.login,sessions.expires FROM sessions JOIN users ON users.id=sessions.owner WHERE digest=? AND expires>?", (digest, self.clock())).fetchone()
        if not row:
            fail(401, "authentication_required")
        return {"user": {"id": row["id"], "login": row["login"]}, "expires_at": row["expires"]}

    def logout(self, token):
        with self.connect(True) as db:
            db.execute("DELETE FROM sessions WHERE digest=?", (sha256(token.encode()).hexdigest(),))

    def project(self, db, owner, project_id):
        identifier(project_id)
        row = db.execute("SELECT document FROM projects WHERE owner=? AND id=?", (owner, project_id)).fetchone()
        return json.loads(row[0]) if row else None

    def get(self, owner, project_id):
        with self.connect() as db:
            project = self.project(db, owner, project_id)
        if project is None:
            fail(404, "project_not_found")
        return project

    def list(self, owner, deleted=False, limit=100, offset=0):
        with self.connect() as db:
            rows = db.execute("SELECT document FROM projects WHERE owner=? AND (json_extract(document,'$.deleted_at') IS NOT NULL)=? ORDER BY id LIMIT ? OFFSET ?", (owner, int(deleted), limit, offset)).fetchall()
        return {"projects": [json.loads(row[0]) for row in rows]}

    def mutate(self, owner, project_id, action, body):
        identifier(project_id)
        allowed = ("operation_id", "base_revision", "metadata", "versions", "selections") if action == "commit" else ("operation_id", "base_revision")
        exact_fields(body, allowed, ("operation_id", "base_revision"))
        operation = identifier(body["operation_id"])
        if type(body["base_revision"]) is not int or body["base_revision"] < 0:
            fail(422, "invalid_revision")
        digest = sha256(canonical({"project_id": project_id, "action": action, "body": body}).encode()).hexdigest()
        with self.connect(True) as db:
            prior = db.execute("SELECT digest,result FROM operations WHERE owner=? AND id=?", (owner, operation)).fetchone()
            if prior:
                if prior["digest"] != digest:
                    fail(409, "operation_id_reused")
                return json.loads(prior["result"])
            project = self.project(db, owner, project_id)
            if project is None:
                if action != "commit" or body["base_revision"] != 0:
                    fail(404, "project_not_found")
                project = {"project_id": project_id, "revision": 0, "metadata": {}, "deleted_at": None, "materials": []}
            if action == "commit" and project["deleted_at"] is not None:
                fail(410, "project_deleted")
            if project["revision"] != body["base_revision"]:
                fail(409, "revision_conflict", revision=project["revision"])
            if action == "delete":
                if project["deleted_at"] is not None:
                    fail(410, "project_deleted")
                project["deleted_at"] = self.clock()
            elif action == "restore":
                if project["deleted_at"] is None:
                    fail(409, "project_not_deleted")
                if self.clock() >= project["deleted_at"] + RETENTION:
                    fail(410, "retention_expired")
                project["deleted_at"] = None
            else:
                self.apply_commit(db, owner, project, body)
            project["revision"] += 1
            result = canonical(project)
            db.execute("INSERT INTO projects VALUES (?,?,?) ON CONFLICT(owner,id) DO UPDATE SET document=excluded.document", (owner, project_id, result))
            db.execute("INSERT INTO operations VALUES (?,?,?,?)", (owner, operation, digest, result))
            return project

    def apply_commit(self, db, owner, project, body):
        metadata = body.get("metadata", {})
        validate_metadata(metadata)
        project["metadata"].update(metadata)
        versions = body.get("versions", [])
        selections = body.get("selections", {})
        if not isinstance(versions, list) or len(versions) > 200 or not isinstance(selections, dict) or len(selections) > 200:
            fail(422, "invalid_materials")
        slots = {slot["material_id"]: slot for slot in project["materials"]}
        known_versions = {version["version_id"] for slot in slots.values() for version in slot["versions"]}
        for version in versions:
            exact_fields(version, ("material_id", "version_id", "kind", "sha256", "size", "mime", "details"), ("material_id", "version_id", "kind", "sha256", "size", "mime"))
            material_id = identifier(version["material_id"])
            version_id = identifier(version["version_id"])
            kind = version["kind"]
            if not isinstance(kind, str) or kind not in KINDS:
                fail(422, "unsupported_kind")
            if not isinstance(version["sha256"], str) or not HASH.fullmatch(version["sha256"]) or type(version["size"]) is not int or not isinstance(version["mime"], str):
                fail(422, "invalid_blob_reference")
            blob = db.execute("SELECT size,mime FROM blobs WHERE owner=? AND project=? AND digest=?", (owner, project["project_id"], version["sha256"])).fetchone()
            if not blob or blob["size"] != version["size"] or blob["mime"] != version["mime"]:
                fail(422, "blob_not_uploaded")
            if (kind in ("cover", "image", "frame")) != (blob["mime"] in IMAGE_MIMES):
                fail(422, "kind_mime_mismatch")
            slot = slots.get(material_id)
            if slot and slot["kind"] != kind:
                fail(409, "material_kind_immutable")
            data = {key: version[key] for key in ("version_id", "sha256", "size", "mime")}
            validate_details(version.get("details", {}))
            data["details"] = version.get("details", {})
            if version_id in known_versions:
                existing = next((item for item in (slot or {}).get("versions", []) if item["version_id"] == version_id), None)
                if not existing or any(existing[key] != value for key, value in data.items()):
                    fail(409, "version_immutable")
                continue
            if len(known_versions) >= 10000:
                fail(413, "version_limit")
            if slot is None:
                slot = {"material_id": material_id, "kind": kind, "current_version_id": version_id, "versions": []}
                slots[material_id] = slot
                project["materials"].append(slot)
            slot["versions"].append({**data, "created_at": self.clock()})
            known_versions.add(version_id)
        for material_id, version_id in selections.items():
            identifier(material_id)
            identifier(version_id)
            slot = slots.get(material_id)
            if not slot or not any(item["version_id"] == version_id for item in slot["versions"]):
                fail(422, "unknown_selection")
            slot["current_version_id"] = version_id

    def blob_path(self, owner, project_id, digest):
        identifier(owner)
        identifier(project_id)
        if not isinstance(digest, str) or not HASH.fullmatch(digest):
            fail(422, "invalid_sha256")
        return self.blob_root / owner / project_id / digest

    def publish_blob(self, owner, project_id, digest, mime, temporary, size):
        target = self.blob_path(owner, project_id, digest)
        if not 0 < size <= self.max_blob_bytes or temporary.stat().st_size != size:
            fail(413, "blob_too_large")
        if sha256(temporary.read_bytes()).hexdigest() != digest:
            fail(422, "sha256_mismatch")
        validate_blob(temporary, mime, size)
        with self.connect(True) as db:
            project = self.project(db, owner, project_id)
            if project is None:
                fail(404, "project_not_found")
            if project["deleted_at"] is not None:
                fail(410, "project_deleted")
            existing = db.execute("SELECT size,mime FROM blobs WHERE owner=? AND project=? AND digest=?", (owner, project_id, digest)).fetchone()
            if existing and (existing["size"] != size or existing["mime"] != mime):
                fail(409, "blob_metadata_immutable")
            used = db.execute("SELECT COALESCE(SUM(size),0) FROM blobs WHERE owner=?", (owner,)).fetchone()[0]
            if not existing and used + size > self.account_quota_bytes:
                fail(413, "account_quota_exceeded")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.is_symlink() or target.stat().st_size != size or sha256(target.read_bytes()).hexdigest() != digest:
                    fail(500, "stored_blob_corrupt")
            if os.name == "posix":
                descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            db.execute("INSERT OR IGNORE INTO blobs VALUES (?,?,?,?,?,?)", (owner, project_id, digest, size, mime, self.clock()))
        return {"sha256": digest, "size": size, "mime": mime}

    def read_blob(self, owner, project_id, digest):
        target = self.blob_path(owner, project_id, digest)
        with self.connect() as db:
            project = self.project(db, owner, project_id)
            if project is None:
                fail(404, "project_not_found")
            if project["deleted_at"] is not None:
                fail(410, "project_deleted")
            referenced = any(version["sha256"] == digest for slot in project["materials"] for version in slot["versions"])
            blob = db.execute("SELECT size,mime FROM blobs WHERE owner=? AND project=? AND digest=?", (owner, project_id, digest)).fetchone()
            if not referenced or not blob:
                fail(404, "blob_not_found")
            try:
                if target.is_symlink() or target.stat().st_size != blob["size"]:
                    fail(500, "stored_blob_corrupt")
                content = target.read_bytes()
            except OSError:
                fail(500, "stored_blob_corrupt")
            if sha256(content).hexdigest() != digest:
                fail(500, "stored_blob_corrupt")
        return content, blob["mime"]

    def collect(self):
        """Purge expired recycle-bin blobs, retaining identity tombstones forever."""
        count = 0
        with self.connect(True) as db:
            rows = db.execute("SELECT owner,id,document FROM projects WHERE json_extract(document,'$.deleted_at') IS NOT NULL").fetchall()
            for row in rows:
                project = json.loads(row["document"])
                if self.clock() < project["deleted_at"] + RETENTION:
                    continue
                blobs = db.execute("SELECT digest FROM blobs WHERE owner=? AND project=?", (row["owner"], row["id"])).fetchall()
                for blob in blobs:
                    self.blob_path(row["owner"], row["id"], blob["digest"]).unlink(missing_ok=True)
                    count += 1
                db.execute("DELETE FROM blobs WHERE owner=? AND project=?", (row["owner"], row["id"]))
                project["materials"] = []
                db.execute("UPDATE projects SET document=? WHERE owner=? AND id=?", (canonical(project), row["owner"], row["id"]))
            db.execute("DELETE FROM sessions WHERE expires <= ?", (self.clock(),))
        return count
