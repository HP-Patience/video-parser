#!/usr/bin/env python3
"""FastAPI entrypoint for the local React workspace."""
from pathlib import Path
import time

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from video_parser.web.routes import router as workspace_router

from video_parser.core.paths import FRONTEND_DIR, instance_id
app = FastAPI(
    title='Video Parser 工作台',
    description='本地视频解析、归档、画面分析、字幕与转写服务',
    version='3.2.0',
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

@app.on_event('startup')
def restore_task_history():
    from video_parser.services import task_runtime
    task_runtime.startup()
    from video_parser.web.task_routes import tasks
    tasks()

@app.on_event('shutdown')
def stop_owned_workers():
    from video_parser.services import task_runtime,whisper_pool
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
            or request.url.path.startswith('/api/workspace/updates')):
        response.headers['Cache-Control']='no-store'
    return response

if (FRONTEND_DIR / 'assets').is_dir():
    app.mount('/assets', StaticFiles(directory=str(FRONTEND_DIR / 'assets')), name='react-assets')


@app.get('/')
async def root():
    index = FRONTEND_DIR / 'index.html'
    if index.is_file():
        return FileResponse(index, headers={'Cache-Control': 'no-cache'})
    return {'status': 'ok', 'message': 'React frontend not built; run npm.cmd --prefix frontend run build.'}


@app.get('/health')
async def health_check():
    return {'status': 'healthy', 'timestamp': int(time.time() * 1000), 'instance': instance_id()}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('video_parser.web.app:app', host='127.0.0.1', port=15001, proxy_headers=False)
