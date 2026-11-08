"""Durable video/AI jobs. Sensitive parsed headers travel over stdin, never journals."""
from dataclasses import dataclass,field
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Lock
import time
from uuid import uuid4
from video_parser.core.paths import ROOT, CACHE_DIR
from video_parser.services import task_runtime,library

CACHE_ROOT=CACHE_DIR / 'managed-jobs'
RECORD_ROOT=ROOT/'.local/tasks/workflows'
jobs={}
lock=Lock()

@dataclass
class Job:
    id:str
    kind:str
    project_id:str
    title:str
    payload:dict=field(default_factory=dict)
    details:dict=field(default_factory=dict)
    state:str='queued'
    message:str='已排队，等待执行…'
    phase:str='queued'
    progress:dict=field(default_factory=dict)
    error_code:str=''
    result:dict=field(default_factory=dict)
    created:float=field(default_factory=time.time)
    created_at:float=field(default_factory=time.monotonic)
    elapsed_seconds:float=0
    stage_timings:dict=field(default_factory=dict)


def get(identifier):
    with lock:
        if identifier not in jobs:jobs[identifier]=task_runtime.restore(RECORD_ROOT,identifier,Job)
        task_runtime.trim_memory(jobs,keep=identifier)
        return jobs[identifier]


def view(job):
    return {**task_runtime.metadata(job),'id':job.id,'kind':job.kind,'project_id':job.project_id,
            'title':job.title,'details':job.details,'state':task_runtime.public_state(job),'message':job.message,'result':job.result}


def start(kind,project_id,title,payload):
    if kind not in {'video','analysis'}:raise ValueError('Unsupported task')
    job=Job(uuid4().hex,kind,project_id,title,payload)
    if kind == 'analysis':
        from video_parser.services import ai_settings, model_calls
        agent = payload.get('agent') or {'engine': 'api'}
        if agent.get('engine', 'api') == 'api':
            settings = ai_settings.current()
            job.payload = {**payload, 'api_binding': model_calls.api_binding(settings)}
        job.details = {key: payload.get(key) for key in ('template', 'source', 'frame_mode', 'count')}
        job.details.update(engine=agent.get('engine', 'api'), model=agent.get('model') or (settings.get('model', '') if agent.get('engine', 'api') == 'api' else ''))
    with lock:
        # Avoid duplicate downloads/paid requests caused by a second click or another browser tab.
        if any(j.project_id==project_id and j.kind==kind and j.state in task_runtime.ACTIVE for j in jobs.values()):
            raise BlockingIOError('这个项目已有同类任务，请在任务中心查看。')
        jobs[job.id]=job
        task_runtime.trim_memory(jobs,keep=job.id)
    try:task_runtime.submit(kind,job,worker,lambda:task_runtime.save(RECORD_ROOT,job))
    except Exception:
        with lock:jobs.pop(job.id,None)
        raise
    return view(job)


def worker(job):
    directory=CACHE_ROOT/job.id;task_runtime.own_directory(directory,job.id)
    stage=directory/'stage.json';result_path=directory/'result.json'
    payload={**job.payload,'kind':job.kind,'project_id':job.project_id,'directory':str(directory)}
    env={**os.environ,'PYTHONUTF8':'1','VIDEO_LIBRARY_ROOT':str(library.LIBRARY_ROOT),'VIDEO_PARSER_STAGE_FILE':str(stage),'TMP':str(directory),'TEMP':str(directory),'TMPDIR':str(directory)}
    command=[sys.executable,'-X','utf8','-m','video_parser.integrations.workflow_worker']
    signature=None
    def consume():
        nonlocal signature
        if not stage.is_file():return
        current=stage.stat().st_mtime_ns
        if current==signature:return
        signature=current
        try:
            row=json.loads(stage.read_text(encoding='utf-8'))
            if row.get('phase') in {'reading','downloading','merging','validating','archiving','analyzing','extracting'}:
                task_runtime.progress(row['phase'],row.get('message'),**row.get('progress',{}))
        except (OSError,ValueError):pass
    task_runtime.progress('downloading' if job.kind=='video' else 'analyzing',
                          '正在下载并校验视频…' if job.kind=='video' else '正在提取画面并分析；不会自动重试付费请求…')
    with subprocess.Popen(command,cwd=ROOT,env=env,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL,text=True,encoding='utf-8') as process:
        task_runtime.communicate(process,7200,consume,input=json.dumps(payload,ensure_ascii=False,default=str))
        consume()
        task_runtime.checkpoint()
        if not result_path.is_file() or result_path.is_symlink() or result_path.stat().st_size>16*1024*1024:
            raise RuntimeError('Worker returned no result')
        result=json.loads(result_path.read_text(encoding='utf-8'))
        if process.returncode or not result.get('ok'):
            job.error_code=result.get('error_code','operation_failed')
            job.message=('视频准备失败：' if job.kind=='video' else '分析失败：')+result.get('message','请检查本机诊断。')
            job.state='failed'
        else:
            job.result=result['result']
            record = job.result.get('record') or {}
            for key in ('template', 'source', 'model'):
                if record.get(key): job.details[key] = record[key]
            if record.get('frame_count') is not None: job.details['count'] = record['frame_count']
            job.state='completed';job.message='视频已校验归档。' if job.kind=='video' else '分析结果与画面已归档。'
    # Only this task's owned scratch directory; the journal already holds the result or the failure.
    # Nothing raised here may demote a completed task: some environments wrap shutil.rmtree with a
    # guard that raises SystemExit, which is not an OSError.
    try:shutil.rmtree(directory)
    except (Exception, SystemExit):
        if job.state=='completed':job.message+=' 临时副本清理失败，可在缓存管理中查看。'
    job.payload.clear()
