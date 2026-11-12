"""JSON-only cloud endpoints behind the existing local request boundary."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from video_parser.services import cloud_sync
from video_parser.web.security import local_request

router = APIRouter(prefix='/api/workspace/cloud', dependencies=[Depends(local_request)])


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Login(Body):
    server: str = Field(max_length=2048)
    login: str = Field(max_length=320)
    password: str = Field(max_length=4096)


class Project(Body):
    project_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')


class Upload(Project):
    independent: bool = False


class Associate(Body):
    local_project_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    cloud_project_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')


class SyncMode(Project):
    enabled: bool


class Pull(Project):
    full: bool = False


class ResolveMetadata(Project):
    field: Literal['title', 'learned']
    choice: Literal['local', 'cloud']


class Version(Project):
    material_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    version_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')


class Task(Body):
    task_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')


def guarded(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except cloud_sync.CloudError as error:
        raise HTTPException(error.status, str(error)) from None
    except KeyError:
        raise HTTPException(404, '项目或材料不存在，请刷新后重试。') from None
    except (ValueError, OSError):
        raise HTTPException(400, '云端操作失败，请检查本机材料和服务器设置后重试。') from None


@router.get('/session')
def session():
    return guarded(cloud_sync.session)


@router.post('/login')
def login(body: Login):
    return guarded(cloud_sync.client().login, body.server, body.login, body.password)


@router.post('/logout')
def logout():
    return guarded(cloud_sync.client().logout)


@router.get('/projects')
def projects():
    return guarded(cloud_sync.client().projects)


@router.post('/upload')
@router.post('/projects/upload')
def upload(body: Upload):
    return guarded(cloud_sync.client().upload, body.project_id, body.independent)


@router.post('/associate')
def associate(body: Associate):
    return guarded(cloud_sync.client().associate, body.local_project_id, body.cloud_project_id)


@router.post('/resolve-metadata')
def resolve_metadata(body: ResolveMetadata):
    return guarded(cloud_sync.client().resolve_metadata, body.project_id, body.field, body.choice)


@router.post('/sync-mode')
@router.post('/projects/sync-mode')
def sync_mode(body: SyncMode):
    return guarded(cloud_sync.client().sync_mode, body.project_id, body.enabled)


@router.post('/pull')
@router.post('/projects/pull')
def pull(body: Pull):
    return guarded(cloud_sync.client().action, 'pull', body.project_id, full=body.full)


@router.get('/transfers')
def transfers():
    return guarded(cloud_sync.client().transfers)


@router.post('/transfers/retry')
def retry(body: Task):
    return guarded(cloud_sync.client().task_control, body.task_id)


@router.post('/transfers/cancel')
def cancel(body: Task):
    return guarded(cloud_sync.client().task_control, body.task_id, True)


@router.get('/versions')
@router.get('/materials/versions')
def versions(project_id: str):
    return guarded(cloud_sync.client().versions, project_id)


@router.post('/select-version')
@router.post('/materials/select-version')
def select_version(body: Version):
    return guarded(cloud_sync.client().action, 'select-version', body.project_id,
                   material_id=body.material_id, version_id=body.version_id)


@router.post('/image')
def image(body: Version):
    return guarded(cloud_sync.client().action, 'image', body.project_id,
                   material_id=body.material_id, version_id=body.version_id)


@router.post('/delete')
@router.post('/projects/delete')
def delete(body: Project):
    return guarded(cloud_sync.client().action, 'delete', body.project_id)


@router.post('/restore')
@router.post('/projects/restore')
def restore(body: Project):
    return guarded(cloud_sync.client().action, 'restore', body.project_id)
