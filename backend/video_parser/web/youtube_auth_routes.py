"""Local-only YouTube cookie endpoints; responses never include cookie values."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from video_parser.services import youtube_auth
from video_parser.web.security import local_request

router = APIRouter(prefix='/api/workspace/youtube-auth', dependencies=[Depends(local_request)])


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Browser(Body):
    browser: str = Field(max_length=32)


class Cookies(Body):
    text: str = Field(min_length=1, max_length=1048576)


def call(function, *args):
    try:
        return function(*args)
    except RuntimeError as error:
        raise HTTPException(400, str(error)) from None
    except OSError:
        raise HTTPException(400, '无法保存 YouTube 登录态，请检查本机目录权限。') from None


@router.get('/status')
def status():
    return youtube_auth.status()


@router.post('/browser')
def choose_browser(body: Browser):
    return call(youtube_auth.choose_browser, body.browser)


@router.post('/import')
def import_from_browser(body: Browser):
    return call(youtube_auth.import_from_browser, body.browser)


@router.post('/cookies')
def import_cookies(body: Cookies):
    return call(youtube_auth.save_text, body.text)


@router.post('/clear')
def clear():
    return call(youtube_auth.clear)
