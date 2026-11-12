# Self-hosted material storage (Debian)

Independent FastAPI process. It never loads the local workspace app, local video library, AI configuration, platform cookies, or task execution endpoints. Use one Debian host with a local filesystem; SQLite WAL is not a shared-network-filesystem database. The default listener is `127.0.0.1:8090`.

## Local validation first

From the repository with Python 3.11+ and existing project dependencies installed:

```sh
PYTHONPATH=backend python -m unittest discover -s tests -p test_cloud_server.py
PYTHONPATH=backend python -m video_parser.cloud --data-dir /absolute/private/cloud-data create-user you@example.com
PYTHONPATH=backend python -m video_parser.cloud --data-dir /absolute/private/cloud-data serve
```

Choose a private data directory separate from the local workspace/library and outside any web root. `create-user` prompts twice with `getpass`; passwords are never command-line arguments. There is no public registration endpoint, built-in account, embedded secret, or environment default password. Passwords use per-account random salts and standard-library scrypt (`n=32768`, `r=8`, `p=3`, 32 MiB working memory). Opaque session tokens are hashed with SHA-256 in SQLite, expire after 30 days, and can be revoked independently. Password minimum is 12 characters. Login names are case-insensitive and may be either e-mail addresses or plain user names such as `Celyn` (2–64 characters of letters, digits, `.`, `_`, `-`, starting with a letter or digit); the stored spelling is preserved. `rename-user <old> <new>` renames an existing account without touching its data. Keep account creation on the server console. Existing-account password recovery/reset is not exposed by this initial version.

Factory use: `video_parser.cloud.app:create_app`, with explicit `data_dir` or `VIDEO_PARSER_CLOUD_DATA`. No cloud process is started by importing the package. The CLI sets a private `0077` umask. All generated file names use validated IDs and server-verified SHA-256, never client file paths.

## Debian service and HTTPS

Install the repository and its existing dependencies into `/opt/video-parser/.venv`. Create a dedicated system account and private state directory, then copy the supplied service units. Adjust installation paths to match your host:

```sh
sudo useradd --system --home-dir /var/lib/video-parser-cloud --shell /usr/sbin/nologin video-parser-cloud
sudo install -d -o video-parser-cloud -g video-parser-cloud -m 0700 /var/lib/video-parser-cloud
sudo -u video-parser-cloud /opt/video-parser/.venv/bin/python -m video_parser.cloud --data-dir /var/lib/video-parser-cloud create-user you@example.com
sudo install -m 0644 deploy/cloud/video-parser-cloud.service /etc/systemd/system/
sudo install -m 0644 deploy/cloud/video-parser-cloud-gc.service /etc/systemd/system/
sudo install -m 0644 deploy/cloud/video-parser-cloud-gc.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now video-parser-cloud.service video-parser-cloud-gc.timer
```

The bootstrap command assumes the project is installed in the virtual environment (`pip install -e /opt/video-parser`) or `PYTHONPATH` is set. Never run the server as root. The service uses one Uvicorn worker; each mutation opens its own SQLite connection, uses `BEGIN IMMEDIATE`, a 30-second busy timeout, and WAL with `synchronous=FULL`. Separate processes still coordinate through SQLite's locks.

Install Caddy using Debian's supported installation procedure. Merge the example Caddy host block into your Caddy configuration and replace `cloud.example.invalid` with a DNS name resolving to the host. Caddy terminates HTTPS and proxies only to loopback. Open public TCP 80/443 for certificate issuance and HTTPS; keep port 8090 private. Do not expose the data directory as static files. No CORS is enabled: the existing local backend is the cloud client.

### Plain-HTTP direct port (domain not ICP-filed in mainland China)

Mainland China hosting providers intercept unfiled domains: HTTP requests on port 80 answer `302` to a provider block page and TLS on 443 is reset even though the local proxy and certificate are healthy. When no ICP-filed domain is available, the supported fallback is to publish the service directly on its own port over plain HTTP: run the unit with `--host 0.0.0.0 --port 8090`, allow TCP 8090 in the cloud provider security group, and sign in from the local app with `http://<public-ip>:8090`. The local client accepts both `http://` and `https://` server addresses (`normalize_server`), and rejects embedded credentials, paths, queries, fragments, and other schemes. Accept the risk this carries: bearer tokens and material contents travel unencrypted over the internet, so use a strong unique password, treat the account as compromised if the host is untrusted, and prefer a filed domain plus Caddy HTTPS as soon as one is available. Removing Caddy also means port 80/443 may stay closed.

Uvicorn proxy header trust and access logging are disabled. Throttles use the actual socket peer and the normalized login name: 10 attempts per 60 seconds each. Behind the supplied proxy this intentionally also acts as a shared login ceiling; forwarded headers cannot spoof client identity. Tune this in code only if a larger deployment needs a trusted-proxy model. Never log request bodies or Authorization headers.

## HTTP contract

All request/response JSON uses UTF-8. Authentication uses `Authorization: Bearer <token>`; no cookie credentials. Timestamps are UTC Unix seconds unless explicitly portable metadata strings. Errors use `{"detail":{"code":"..."}}`; a stale project also includes `revision`. Health at `GET /health` returns `{"ok":true}` without disclosing state.

| Method and path | Request | Response |
| --- | --- | --- |
| `POST /v1/auth/login` | `{login,password}` (`{email,password}` still accepted) | `{token,expires_at,user:{id,login}}` |
| `GET /v1/auth/session` | Bearer | `{user:{id,login},expires_at}` |
| `DELETE /v1/auth/session` | Bearer | `{ok:true}`; revokes this session |
| `GET /v1/projects?deleted=false&limit=100&offset=0` | Bearer | `{projects:[project,...]}`; deterministic ID order |
| `GET /v1/projects/{project_id}` | Bearer | `project`, including tombstone if deleted |
| `POST /v1/projects/{project_id}/commit` | `{operation_id,base_revision,metadata?,versions?,selections?}` | `project` |
| `PUT /v1/projects/{project_id}/blobs/{sha256}` | Raw body, correct `Content-Type` | `{sha256,size,mime}` |
| `GET /v1/projects/{project_id}/blobs/{sha256}` | Bearer | Verified bytes; requires a published version reference |
| `POST /v1/projects/{project_id}/delete` | `{operation_id,base_revision}` | Deleted `project` |
| `POST /v1/projects/{project_id}/restore` | `{operation_id,base_revision}` | Restored `project` within 30 days |

`project`:

```json
{
  "project_id": "example-project",
  "revision": 2,
  "metadata": {"title": "Example", "source_key": "bilibili:BV123:p1", "learned": false},
  "deleted_at": null,
  "materials": [{
    "material_id": "subtitle-en",
    "kind": "subtitle",
    "current_version_id": "version-one",
    "versions": [{
      "version_id": "version-one",
      "sha256": "64-lowercase-hex-characters",
      "size": 123,
      "mime": "text/plain",
      "details": {"filename": "subtitle.srt", "language": "en"},
      "created_at": 1800000000
    }]
  }]
}
```

Create an empty project using a commit with `base_revision:0`, then upload its blobs, then publish versions with the returned revision. Version input is `{material_id,version_id,kind,sha256,size,mime,details?}`. `selections` maps material IDs to version IDs. The first version initializes a new slot's current selection; later versions never change selection unless explicitly included in `selections`. Existing version IDs are immutable, including details. Equal SHA-256 across projects/accounts grants no read permission.

Project metadata is a patch, not a replacement, and permits only `title,source_key,learned,platform,video_id,part,source_url,created_at`. `part` is 1..100000. Source URLs must be HTTP(S), without userinfo/fragments, and have no query except a single identity parameter: YouTube `v`, or Bilibili `p`. Authentication, signed query parameters, cookie/config fields, absolute local paths, and unknown fields are rejected. Client export must select business fields explicitly; do not submit arbitrary local manifests.

Version details are bounded to 64 KiB. Allowed fields: `filename,lang,language,source_language,source_sha256,video_part,format,report_type,generated_at,created_at,record,conversation,sequence,question,source,title,model,template,frames,links,source_material_id,origin,label,template_id,template_name,context_after,origin_record,position,images`. Filenames are basenames only. `template` is a string or `{id,name}`. `frames` is a bounded array of `{material_id,version_id,time?}`; `links`/`images` use `{material_id,version_id,role?}`. References carry IDs instead of local paths. Generic strings are at most 500 characters; question/context_after at most 4000; arrays at most 200. Exporters must omit unsupported runtime configuration and credentials.

Kinds: `subtitle,transcription,correction,translation,cover,image,frame,ai_text,ai_result,analysis,analysis-followup,conversation,conversation-message`. Images/frames/covers require real PNG/JPEG/WebP content verified with Pillow; animated images and images over 40 million pixels are rejected. Other kinds require UTF-8 text/Markdown/JSON/SRT/VTT. JSON must parse. Audio, video, SVG, unknown MIME/kinds, binary disguised as text, and incorrect hashes are rejected. Limits: 32 MiB per blob, 4 MiB for text, 2 GiB stored blobs per account, 1 MiB per JSON request, 200 versions per commit, 10000 versions per project. Account quota counts staged-but-not-published uploads and all historical versions. Limits may be changed explicitly through factory arguments for blob/quota or code for structural limits.

IDs and operation IDs match `[A-Za-z0-9_-]{1,128}`. Operation IDs are unique per account across projects/actions. A successful operation records its digest and exact result in the same transaction. Retry identical operations after a lost response with the same ID and identical body; the result does not advance revision again. Reusing the ID with different contents returns `409 operation_id_reused`. Rejected requests do not consume the ID. Stale revision returns `409 revision_conflict`; download latest state before explicitly rebasing a new attempt. A replay returns the original historical result, so clients should reconcile latest state before making subsequent decisions.

## Deletion, maintenance and backup

Delete writes a tombstone and advances revision. Uploads/commits to deleted projects return `410 project_deleted`, including old create operations with new operation IDs. Restore checks revision and the 30-day boundary. At/after the boundary restoration returns `410 retention_expired`. Run `gc` daily through the supplied timer. It removes expired recycled blobs, clears their material manifests, and keeps project identity tombstones permanently so stale devices cannot recreate them. Historical versions in live projects have no automatic expiry. Unreferenced uploaded blobs remain private and count toward quota until the project is recycled and expires.

A crashed upload may leave private files under `staging/`; with the service stopped these can be removed. A crash between immutable file publication and SQLite commit may leave an unregistered content file. Keep it until explicit offline reconciliation or project-directory maintenance; never recursively clear live blob directories. File corruption/missing data returns `500 stored_blob_corrupt`; the service never silently repairs or overwrites a published object.

For a simple consistent backup, stop the service and GC timer, back up the **entire** private data directory (SQLite database, WAL/SHM if present, and blobs), then restart. Encrypt backups, restrict access, test restore to a separate directory, and retain copies before upgrades. Copying a live SQLite main file without its WAL is not a consistent backup. Password hashes and session digests are sensitive backup data even though plaintext secrets are not stored. There is no automatic off-host backup or disk quota reservation.

This repository change supplies local code and deployment examples. The reference deployment uses a user-level systemd unit with a `PYTHONPATH` install (no pip network access on the target) and Debian's packaged `python3-fastapi`/`python3-uvicorn`/`python3-pil`; substitute your own paths. Creating accounts, DNS/TLS/firewall settings, and credentials remain environment decisions and are not provisioned by this repository.
