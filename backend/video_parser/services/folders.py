"""User-chosen output folders for subtitles/transcripts.

Only folders the user picked in the native Windows dialog are writable; the list is kept locally
so a chosen folder keeps working after a restart. Files are never overwritten.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Lock

from video_parser.core.paths import ROOT
from video_parser.services import library

STATE = ROOT / '.local/state/output-folders.json'
MAX_FOLDERS = 20
dialog_lock = Lock()
state_lock = Lock()

DIALOG = r'''
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true; $owner.ShowInTaskbar = $false; $owner.Opacity = 0
$owner.StartPosition = 'CenterScreen'; $owner.Show(); $owner.Activate()
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '__DESCRIPTION__'
$dialog.ShowNewFolderButton = $true
if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dialog.SelectedPath }
$owner.Close()
'''


def relocated(folder):
    from video_parser.services.library_layout import restore_task_paths
    return restore_task_paths({'path': str(folder)})['path']


def remembered():
    try:
        data = json.loads(STATE.read_text(encoding='utf-8'))
        return [relocated(item) for item in data.get('folders', []) if isinstance(item, str)][:MAX_FOLDERS]
    except (OSError, ValueError, AttributeError):
        return []


def remember(path):
    with state_lock:
        folders = [str(path)] + [item for item in remembered() if Path(item) != Path(path)]
        STATE.parent.mkdir(parents=True, exist_ok=True)
        library.atomic_json(STATE, {'folders': folders[:MAX_FOLDERS]})


def forget(path):
    path = relocated(path)
    with state_lock:
        folders = [item for item in remembered() if Path(item) != Path(path)]
        STATE.parent.mkdir(parents=True, exist_ok=True)
        library.atomic_json(STATE, {'folders': folders})


def listing():
    return [{'path': item, 'exists': Path(item).is_dir()} for item in remembered()]


def pick(description='选择字幕和转写结果的保存位置', remember_choice=True):
    """Open the native folder dialog on this machine. Returns the chosen path, or None when cancelled."""
    if sys.platform != 'win32':
        raise NotImplementedError('选择文件夹窗口目前只支持 Windows。')
    if not dialog_lock.acquire(blocking=False):
        raise BlockingIOError('已经打开了一个选择文件夹窗口，请先在任务栏找到它并完成选择。')
    description = ' '.join(str(description).split())[:120] or '选择文件夹'
    dialog = DIALOG.replace('__DESCRIPTION__', description.replace("'", "''"))
    try:
        encoded = base64.b64encode(dialog.encode('utf-16-le')).decode()
        result = subprocess.run(['powershell.exe', '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=900,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        return None
    finally:
        dialog_lock.release()
    chosen = (result.stdout or '').strip().splitlines()
    if not chosen:
        return None
    path = Path(chosen[-1].strip())
    if not path.is_absolute() or not path.is_dir():
        raise ValueError('所选位置不是可用的文件夹。')
    path = path.resolve()
    if remember_choice:
        remember(path)
    return str(path)

def check(folder):
    """A destination must be a folder the user picked before, and still exist."""
    if not folder:
        raise ValueError('请先选择保存文件夹。')
    path = Path(relocated(folder))
    if not path.is_absolute():
        raise ValueError('保存位置必须是完整路径。')
    if not any(Path(item) == path for item in remembered()):
        raise PermissionError('只能保存到通过“选择文件夹”选过的位置，请重新选择。')
    path = path.resolve()
    if not path.is_dir():
        raise FileNotFoundError('保存文件夹不存在，可能已被移动或删除，请重新选择。')
    return path


def publish(folder, source, name):
    """Copy source into folder as name; when the name is taken, add (2), (3)… instead of overwriting."""
    directory = check(folder)
    if not isinstance(name, str) or not name or name in ('.', '..') or any(char in name for char in '/\\:\x00'):
        raise ValueError('保存文件名不能包含路径。')
    stem, suffix = (name.rsplit('.', 1) + [''])[:2]
    suffix = '.' + suffix if suffix else ''
    temporary = identity = None
    try:
        # A partial copy must never have a final subtitle/transcript filename.
        with tempfile.NamedTemporaryFile(mode='wb', prefix='.video-parser-', suffix='.part',
                                         dir=directory, delete=False) as writer:
            temporary = Path(writer.name)
            identity = os.fstat(writer.fileno())
            with Path(source).open('rb') as reader:
                shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        if not os.path.samestat(identity, temporary.stat(follow_symlinks=False)):
            raise OSError('发布临时文件已被替换，未提交。')
        for index in range(1, 1000):
            target = directory / (name if index == 1 else f'{stem} ({index}){suffix}')
            try:
                # Windows rename and POSIX link both fail atomically if target exists.
                # POSIX rename/replace would overwrite; never use them here.
                if os.name == 'nt':
                    os.rename(temporary, target)
                else:
                    os.link(temporary, target)
                return target
            except FileExistsError:
                continue
        raise FileExistsError('同名文件过多，请换一个文件夹。')
    finally:
        if temporary is not None:
            try:
                # Only remove the temporary inode created by this invocation.
                if os.path.samestat(identity, temporary.stat(follow_symlinks=False)):
                    temporary.unlink()
            except FileNotFoundError:
                pass
