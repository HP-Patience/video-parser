"""JSON routes for local-only video association."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from video_parser.services import local_media
from video_parser.web.security import local_request

router = APIRouter(prefix='/api/workspace/local-media', dependencies=[Depends(local_request)])


class PickerBody(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    path: str | None = None


class StartBody(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=4096)


class ImportBody(BaseModel):
    path: str | None = Field(default=None, max_length=4096)


def _guard(action, *args):
    try:
        return action(*args)
    except KeyError:
        raise HTTPException(404, '找不到本地视频任务或项目。') from None
    except BlockingIOError as error:
        raise HTTPException(409, str(error)) from None
    except local_media.LocalMediaError as error:
        raise HTTPException(400, str(error)) from None
    except FileNotFoundError:
        raise HTTPException(404, '文件或项目不存在。') from None
    except (OSError, RuntimeError) as error:
        raise HTTPException(400, str(error) or '本地视频校验失败。') from None


@router.post('/picker')
def picker(body: PickerBody):
    # Missing path opens the native dialog; manual absolute paths are a fallback.
    return _guard(local_media.picker, body.project_id, body.path)


@router.post('/start', status_code=202)
def start(body: StartBody):
    return _guard(local_media.start, body.project_id, body.path)


@router.post('/import')
def import_local(body: ImportBody | None = None):
    # No body path opens the native dialog; the file becomes a project in the library.
    return _guard(local_media.import_video, body.path if body else None)


@router.get('/tasks/{task_id}')
def progress(task_id: str):
    return _guard(local_media.view, task_id)


@router.post('/tasks/{task_id}/cancel')
def cancel(task_id: str):
    return _guard(local_media.cancel, task_id)
