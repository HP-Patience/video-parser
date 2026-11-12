"""Independent FastAPI service for account-scoped cloud material storage."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import os
import tempfile

from fastapi import Depends, FastAPI, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from .store import Store, fail


async def json_body(request: Request):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        fail(415, "json_required")
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 1024 * 1024:
            fail(413, "request_too_large")
    try:
        body = json.loads(content, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
    except (UnicodeError, ValueError, RecursionError):
        fail(422, "invalid_json")
    if not isinstance(body, dict):
        fail(422, "json_object_required")
    return body


def create_app(data_dir: Path | str | None = None, **store_options) -> FastAPI:
    """Factory; data_dir must be configured explicitly, outside local workspace state."""
    configured = data_dir or os.environ.get("VIDEO_PARSER_CLOUD_DATA")
    if not configured:
        raise RuntimeError("Set VIDEO_PARSER_CLOUD_DATA or pass data_dir explicitly")
    store = Store(Path(configured), **store_options)
    app = FastAPI(title="Video Parser Material Storage", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store

    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response

    def authenticated(request: Request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            fail(401, "authentication_required")
        return store.session(header[7:])

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/v1/auth/login")
    async def login(request: Request):
        body = await json_body(request)
        fields = {"login", "password"}
        if set(body) not in (fields, {"email", "password"}):
            fail(422, "invalid_fields")
        login = body.get("login", body.get("email"))
        client = request.client.host if request.client else "unknown"
        return await run_in_threadpool(store.login, login, body["password"], client)

    @app.get("/v1/auth/session")
    def session(account=Depends(authenticated)):
        return account

    @app.delete("/v1/auth/session")
    def logout(request: Request, account=Depends(authenticated)):
        store.logout(request.headers["authorization"][7:])
        return {"ok": True}

    @app.get("/v1/projects")
    def projects(deleted: bool = False, limit: int = 100, offset: int = 0, account=Depends(authenticated)):
        if not 1 <= limit <= 100 or not 0 <= offset <= 1_000_000:
            fail(422, "invalid_pagination")
        return store.list(account["user"]["id"], deleted, limit, offset)

    @app.get("/v1/projects/{project_id}")
    def project(project_id: str, account=Depends(authenticated)):
        return store.get(account["user"]["id"], project_id)

    async def mutate(request, project_id, action, account):
        body = await json_body(request)
        return await run_in_threadpool(store.mutate, account["user"]["id"], project_id, action, body)

    @app.post("/v1/projects/{project_id}/commit")
    async def commit(request: Request, project_id: str, account=Depends(authenticated)):
        return await mutate(request, project_id, "commit", account)

    @app.post("/v1/projects/{project_id}/delete")
    async def delete(request: Request, project_id: str, account=Depends(authenticated)):
        return await mutate(request, project_id, "delete", account)

    @app.post("/v1/projects/{project_id}/restore")
    async def restore(request: Request, project_id: str, account=Depends(authenticated)):
        return await mutate(request, project_id, "restore", account)

    @app.put("/v1/projects/{project_id}/blobs/{digest}")
    async def upload(request: Request, project_id: str, digest: str, account=Depends(authenticated)):
        owner = account["user"]["id"]
        store.blob_path(owner, project_id, digest)
        project = await run_in_threadpool(store.get, owner, project_id)
        if project["deleted_at"] is not None:
            fail(410, "project_deleted")
        mime = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        length = request.headers.get("content-length")
        if length is not None:
            try:
                declared = int(length)
            except ValueError:
                fail(422, "invalid_content_length")
            if declared < 0 or declared > store.max_blob_bytes:
                fail(413, "blob_too_large")
        descriptor, name = tempfile.mkstemp(dir=store.temp_root)
        temporary = Path(name)
        try:
            hashed = sha256()
            size = 0
            with os.fdopen(descriptor, "wb") as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > store.max_blob_bytes:
                        fail(413, "blob_too_large")
                    hashed.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if size == 0 or (length is not None and size != declared):
                fail(422, "invalid_blob_size")
            if hashed.hexdigest() != digest:
                fail(422, "sha256_mismatch")
            return await run_in_threadpool(store.publish_blob, owner, project_id, digest, mime, temporary, size)
        finally:
            temporary.unlink(missing_ok=True)

    @app.get("/v1/projects/{project_id}/blobs/{digest}")
    def download(project_id: str, digest: str, account=Depends(authenticated)):
        content, mime = store.read_blob(account["user"]["id"], project_id, digest)
        return Response(content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{digest}"'})

    return app
