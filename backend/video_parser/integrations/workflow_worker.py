"""Disposable process for downloads/AI: killing its process tree stops local work."""
import json
from pathlib import Path
import sys
from video_parser.services import videos,library,task_runtime


def execute(payload):
    directory=Path(payload['directory']);project_id=payload['project_id']
    project=library.get(project_id)
    if payload['kind']=='video':
        parsed=videos.ParsedVideo(**payload['parsed'])
        selected=videos.select_quality(parsed,payload['quality_id'])
        if library.ensure(selected.metadata)['id']!=project_id:raise ValueError('Project identity mismatch')
        task_runtime.progress('downloading','正在下载所选视频…')
        result=videos.archive_video(selected,staging_root=directory)
        from video_parser.web.routes import media_response
        return media_response(project_id,result['relative'],payload['quality_id'])
    if payload['kind']!='analysis' or payload.get('consent') is not True:raise ValueError('Explicit consent required')
    from video_parser.services import ai_analysis
    agent_options = {'agent': payload['agent']} if payload.get('agent') else {}
    if payload.get('mode')=='conversation':
        from video_parser.services import conversations
        session=conversations.ask(project_id,payload['conversation'],payload['question'],payload['resource'],payload['position'],directory,images=payload.get('images'),**agent_options)
        return {'text':session['messages'][-1]['answer'],'conversation':session,'mode':'conversation',
                'project':library.public(library.get(project_id))}
    if payload.get('mode')=='ask':
        record=ai_analysis.ask(project_id,payload['record'],payload['question'],directory,**agent_options)
    else:
        if project['resources'][payload['resource']]['kind']!='video':raise ValueError('Not video')
        record=ai_analysis.run(project_id,payload,directory)
    return {'text':record['text'],'record':record,'mode':payload.get('mode') or 'analyze','frame_count':record['frame_count'],
            'report_url':f"/api/workspace/projects/{project_id}/files/analysis/{record['id']}.md",
            'project':library.public(library.get(project_id))}


def main():
    videos.load_environment()
    payload=json.loads(sys.stdin.read(4*1024*1024))
    directory=Path(payload['directory'])
    from video_parser.services.workflows import CACHE_ROOT
    if (not directory.resolve().is_relative_to(CACHE_ROOT.resolve()) or directory.is_symlink()
            or not (directory/'.task-owner.json').is_file()):raise ValueError('Unsafe task directory')
    try:
        from video_parser.services import model_calls
        with model_calls.bound_api(payload.get('api_binding')):
            result=execute(payload)
        task_runtime.atomic(directory/'result.json',{'ok':True,'result':result})
        return 0
    except Exception as error:
        code,message=task_runtime.classify(error)
        task_runtime.atomic(directory/'result.json',{'ok':False,'error_code':code,'message':message,'error_type':type(error).__name__})
        return 1

if __name__=='__main__':raise SystemExit(main())
