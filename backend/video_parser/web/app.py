#!/usr/bin/env python3
"""FastAPI entrypoint for the local React workspace."""
from pathlib import Path
import logging
import os
import subprocess
import threading
import time
import webbrowser

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from video_parser.web.routes import router as workspace_router

from video_parser.core.paths import FRONTEND_DIR, instance_id
logger = logging.getLogger(__name__)
app = FastAPI(
    title='Video Parser 工作台',
    description='本地视频解析、归档、画面分析、字幕与转写服务',
    version='3.4.1',
)
app.include_router(workspace_router)
from video_parser.web.task_routes import router as task_router
app.include_router(task_router)
from video_parser.web.account_routes import router as account_router
app.include_router(account_router)
from video_parser.web.analysis_routes import router as analysis_router
app.include_router(analysis_router)
from video_parser.web.study_routes import router as study_router
app.include_router(study_router)
from video_parser.web.naming_routes import router as naming_router
app.include_router(naming_router)
from video_parser.web.embedding_routes import router as embedding_router
app.include_router(embedding_router)
from video_parser.web.usage_routes import router as usage_router
app.include_router(usage_router)
from video_parser.web.archive_routes import router as archive_router
app.include_router(archive_router)
from video_parser.web.cloud_routes import router as cloud_router
app.include_router(cloud_router)
from video_parser.web.local_media_routes import router as local_media_router
app.include_router(local_media_router)
from video_parser.web.youtube_auth_routes import router as youtube_auth_router
app.include_router(youtube_auth_router)

# The cookie-bridge extension lives on chrome-extension://, a different origin from the workspace.
# Only that scheme is allowed, and only for the extension's JSON POST. allow_private_network covers
# Chrome's preflight when an extension reaches loopback.
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware, allow_origin_regex=r'^(chrome|moz)-extension://.*$',
                   allow_methods=['POST', 'OPTIONS'], allow_headers=['content-type'],
                   allow_private_network=True, max_age=600)

def open_in_default_browser(address):
    """Hand the address to the default browser.

    On Windows `cmd /c start` is used instead of webbrowser.open: os.startfile goes through
    ShellExecute and is silently accepted but dropped by some existing browser instances (observed
    with a Chrome started by a third-party manager), while `start` still reaches them."""
    if os.name != 'nt':
        return webbrowser.open(address)
    try:
        # No manual quoting: subprocess escapes the arguments itself, and an extra pair of quotes
        # would reach `start` verbatim as part of the path.
        subprocess.Popen(['cmd', '/c', 'start', '', address],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return True
    except OSError:
        return webbrowser.open(address)


def maybe_open_browser(environ=None):
    """Open the workspace once, when the launcher asked for it (VIDEO_PARSER_OPEN_BROWSER=1).

    The server opens it itself instead of having the launcher spawn a second PowerShell: the browser
    then belongs to a process that is already running interactively, and nothing relies on a hidden
    child process being allowed to raise a window."""
    environ = os.environ if environ is None else environ
    if environ.get('VIDEO_PARSER_OPEN_BROWSER') != '1':
        return False
    address = environ.get('VIDEO_PARSER_OPEN_URL') or 'http://127.0.0.1:15001'
    try:
        delay = max(float(environ.get('VIDEO_PARSER_OPEN_DELAY') or 1), 0)
    except ValueError:
        delay = 1.0

    def open_later():
        time.sleep(delay)
        try:
            opened = open_in_default_browser(address)
            logger.info('open-browser: %s -> %s', address, 'ok' if opened else 'reported failure')
        except Exception as error:
            # A headless session may have no browser; the console already prints the address.
            logger.warning('open-browser failed for %s: %s', address, error)

    threading.Thread(target=open_later, name='open-browser', daemon=True).start()
    return True


@app.on_event('startup')
def restore_task_history():
    from video_parser.services import task_runtime
    task_runtime.startup()
    from video_parser.web.task_routes import tasks
    tasks()
    from video_parser.services import cloud_sync
    cloud_sync.start()
    maybe_open_browser()

@app.on_event('shutdown')
def stop_owned_workers():
    from video_parser.services import task_runtime,whisper_pool,cloud_sync
    cloud_sync.stop()
    stopped=task_runtime.shutdown()
    if stopped:whisper_pool.close()

@app.middleware('http')
async def refresh_shared_library(request, call_next):
    if request.url.path.startswith('/api/'):
        from video_parser.services import library
        library.refresh_library_root()
    return await call_next(request)


@app.middleware('http')
async def request_identifier(request, call_next):
    from uuid import uuid4
    identifier=uuid4().hex
    request.state.request_id=identifier
    response=await call_next(request)
    response.headers['X-Request-ID']=identifier
    if (request.url.path.startswith('/api/workspace/tasks') or request.url.path.startswith('/api/workspace/cache')
            or '/conversations' in request.url.path or request.url.path.startswith('/api/workspace/ai/agents')
            or request.url.path.startswith('/api/workspace/updates') or request.url.path.startswith('/api/workspace/cloud')):
        response.headers['Cache-Control']='no-store'
    return response

class HashedAssets(StaticFiles):
    """Vite asset names carry a content hash, so a year of caching is safe: a rebuild changes the URL."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return response


if (FRONTEND_DIR / 'assets').is_dir():
    app.mount('/assets', HashedAssets(directory=str(FRONTEND_DIR / 'assets')), name='react-assets')


@app.get('/')
async def root():
    index = FRONTEND_DIR / 'index.html'
    if index.is_file():
        # Never let a rebuilt bundle hide behind a cached shell: the HTML names the hashed assets.
        return FileResponse(index, headers={'Cache-Control': 'no-store'})
    return {'status': 'ok', 'message': 'React frontend not built; run npm.cmd --prefix frontend run build.'}


@app.get('/health')
async def health_check():
    return {'status': 'healthy', 'timestamp': int(time.time() * 1000), 'instance': instance_id()}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('video_parser.web.app:app', host='127.0.0.1', port=15001, proxy_headers=False)
