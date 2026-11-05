"""Strict same-origin APIs for personal learning state, independent of AI."""
from typing import Annotated, Literal
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from video_parser.web.security import local_request
from video_parser.services import anki_export, attachments, study

router = APIRouter(prefix='/api/workspace', dependencies=[Depends(local_request)])
Project = Annotated[str, Path(pattern=r'^[a-f0-9]{32}$')]
NoteId = Annotated[str, Path(pattern=r'^[a-f0-9]{32}$')]
RecordId = Annotated[str, Path(pattern=r'^[0-9A-Za-z_-]{1,64}$')]
Resource = Annotated[str, Query(min_length=1, max_length=512)]

class ResourceBody(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    resource: str = Field(min_length=1, max_length=512)
    expected_revision: int = Field(ge=0, strict=True)

class ProgressBody(ResourceBody):
    position: float = Field(ge=0, le=study.MAX_TIME, strict=True)
    duration: float = Field(gt=0, le=study.MAX_TIME, strict=True)

class ImageRef(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(pattern=r'^[a-f0-9]{64}\.(?:jpg|png|webp|gif)$')

class NoteBody(ResourceBody):
    time: float = Field(ge=0, le=study.MAX_TIME, strict=True)
    text: str = Field(default='', max_length=5000)
    images: list[ImageRef] = Field(default_factory=list, max_length=attachments.MAX_COUNT)

class ImageBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data: str = Field(min_length=32, max_length=8 * 1024 * 1024)

class QuizBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resource: str = Field(min_length=1, max_length=512)
    answers: dict[str, int] = Field(default_factory=dict)


def handled(function, *args):
    try: return function(*args)
    except study.Conflict as error: raise HTTPException(409, str(error)) from None
    except KeyError: raise HTTPException(404, '项目或笔记不存在。') from None
    except ValueError as error: raise HTTPException(400, str(error)) from None
    except (OSError, TimeoutError): raise HTTPException(503, '学习数据暂时无法保存，请检查本地磁盘并重试；草稿不会自动丢弃。') from None

@router.get('/projects/{identifier}/study/progress')
def get_progress(identifier: Project, resource: Resource):
    return handled(study.load, identifier, resource)['progress']

@router.post('/projects/{identifier}/study/progress')
def save_progress(identifier: Project, body: ProgressBody):
    return handled(study.progress, identifier, body.resource, body.position, body.duration, body.expected_revision)

@router.get('/projects/{identifier}/study/notes/export')
def export_notes(identifier: Project, resource: Resource, format: Literal['md', 'csv', 'anki'] = 'md'):
    """Notes stay personal data: the same read path as the note list, no AI involved."""
    filename = handled(study.export_filename, identifier)
    if format == 'anki':
        content = anki_export.cards_csv(handled(study.note_cards, identifier, resource))
        media = 'text/csv; charset=utf-8'
    elif format == 'csv':
        content = handled(study.notes_csv, identifier, resource)
        media = 'text/csv; charset=utf-8'
    else:
        content, _ = handled(study.export_markdown, identifier, resource)
        content, media = content.encode('utf-8'), 'text/markdown; charset=utf-8'
    name = f'{filename}.{format}'
    return Response(content, media_type=media, headers={
        'Content-Disposition': f"attachment; filename=\"notes.{format}\"; filename*=UTF-8''{quote(name)}",
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/projects/{identifier}/study/notes')
def get_notes(identifier: Project, resource: Resource):
    return {'notes': handled(study.notes, identifier, resource)}

@router.post('/projects/{identifier}/study/notes/{note_id}')
def save_note(identifier: Project, note_id: NoteId, body: NoteBody):
    return handled(study.save_note, identifier, body.resource, note_id, body.time, body.text, body.expected_revision,
                   [image.model_dump() for image in body.images])


@router.post('/projects/{identifier}/attachments')
def upload_attachment(identifier: Project, body: ImageBody):
    """Paste-to-upload: the browser sends one data URL, the archive keeps the bytes."""
    return handled(attachments.save, identifier, body.data)


@router.get('/projects/{identifier}/attachments/{name}')
def get_attachment(identifier: Project, name: str):
    path = handled(attachments.path, identifier, name)
    # Names are content hashes, so the bytes behind one URL never change.
    return Response(path.read_bytes(), media_type=attachments.media_type(name),
                    headers={'Cache-Control': 'private, max-age=31536000, immutable',
                             'X-Content-Type-Options': 'nosniff'})

@router.post('/projects/{identifier}/study/notes/{note_id}/delete')
def delete_note(identifier: Project, note_id: NoteId, body: ResourceBody):
    return handled(study.delete_note, identifier, body.resource, note_id, body.expected_revision)


@router.get('/projects/{identifier}/study/quiz/{record_id}')
def get_quiz(identifier: Project, record_id: RecordId, resource: Resource):
    """Answers for one analysis record; empty when the learner has not started."""
    return handled(study.quiz, identifier, resource, record_id)


@router.post('/projects/{identifier}/study/quiz/{record_id}')
def save_quiz(identifier: Project, record_id: RecordId, body: QuizBody):
    return handled(study.save_quiz, identifier, body.resource, record_id, body.answers)
